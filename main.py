import os
import sys
import time
import uuid
import argparse
import logging
import subprocess
import http.cookiejar
from urllib.parse import urljoin
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Optional imports for scraping, so Git init can run even without them
try:
    import requests
    from bs4 import BeautifulSoup, NavigableString
    import docx
    from docx.shared import Inches
except ImportError:
    requests = None
    BeautifulSoup = None
    docx = None
    NavigableString = None

def init_git_repo(base_dir: Path):
    """Initializes Git repository if it doesn't exist, creates boilerplate files and first commit."""
    if (base_dir / '.git').exists():
        return

    logging.info("Initializing new Git repository...")
    
    # Create boilerplate files
    gitignore_path = base_dir / '.gitignore'
    if not gitignore_path.exists():
        gitignore_path.write_text("data/\n__pycache__/\n.env\nvenv/\n*.pyc\ncookies.txt\n", encoding='utf-8')
        logging.info("Created .gitignore")

    readme_path = base_dir / 'README.md'
    if not readme_path.exists():
        readme_path.write_text(
            "# GetCourse Scraper\n\n"
            "Clean and scalable scraper for GetCourse (niifittech.ru).\n\n"
            "## Setup\n"
            "1. `python3 -m venv venv`\n"
            "2. `source venv/bin/activate`\n"
            "3. `pip install -r requirements.txt`\n"
            "4. Export your GetCourse cookies into `cookies.txt` (using a browser extension like 'Get cookies.txt LOCALLY').\n",
            encoding='utf-8'
        )
        logging.info("Created README.md")

    requirements_path = base_dir / 'requirements.txt'
    if not requirements_path.exists():
        requirements_path.write_text("requests\nbeautifulsoup4\npython-docx\nyt-dlp\n", encoding='utf-8')
        logging.info("Created requirements.txt")

    # Create empty data folder just in case (ignored by git, but good for structure)
    data_dir = base_dir / 'data'
    data_dir.mkdir(exist_ok=True)

    # Git commands
    try:
        subprocess.run(["git", "init"], cwd=base_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", ".gitignore", "README.md", "requirements.txt", Path(__file__).name], cwd=base_dir, check=True, capture_output=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], cwd=base_dir, capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", "Initial commit: Scraper engine structure"], cwd=base_dir, check=True, capture_output=True)
            logging.info("Committed initial repository structure.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Git command failed: {e.stderr.decode('utf-8') if e.stderr else str(e)}")
    except FileNotFoundError:
        logging.error("Git is not installed or not found in PATH.")

def clean_filename(name: str) -> str:
    """Cleans a string to be used as a safe filename."""
    safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_', '(', ')')).strip()
    return safe.replace(' ', '_').strip('_')

def get_session(base_dir: Path):
    """Creates a requests session and loads cookies if available."""
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    session.headers.update(headers)
    
    cookie_file = base_dir / 'cookies.txt'
    if cookie_file.exists():
        try:
            cj = http.cookiejar.MozillaCookieJar(cookie_file)
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(cj)
            logging.info("Loaded cookies from cookies.txt")
        except Exception as e:
            logging.error(f"Failed to load cookies.txt: {e}")
    else:
        logging.warning("No cookies.txt found. The scraper might not have access to closed trainings.")
        
    return session

def process_element(elem, doc, session, lesson_dir):
    """Recursively processes elements to preserve order of text and images."""
    if isinstance(elem, NavigableString):
        text = str(elem).strip()
        if text:
            doc.add_paragraph(text)
        return

    if elem.name in ['script', 'style', 'iframe']:
        return

    if elem.name == 'img':
        src = elem.get('src')
        if src:
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = 'https://niifittech.ru' + src
            
            try:
                r = session.get(src, stream=True, timeout=15)
                r.raise_for_status()
                
                temp_img = lesson_dir / f"temp_{uuid.uuid4().hex}.jpg"
                with open(temp_img, 'wb') as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
                
                try:
                    doc.add_picture(str(temp_img), width=Inches(6))
                except Exception as pic_err:
                    logging.error(f"Failed to insert image to doc: {pic_err}")
                
                if temp_img.exists():
                    os.remove(temp_img)
            except Exception as e:
                logging.error(f"Failed to download image {src}: {e}")
        return

    if elem.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        doc.add_heading(elem.get_text(strip=True), level=int(elem.name[1]))
        return

    # If it's a structural element containing text and NO images, just grab text
    if elem.name in ['p', 'li', 'div', 'span', 'strong', 'b', 'i', 'em']:
        if not elem.find('img'):
            text = elem.get_text(strip=True)
            if text:
                if elem.name == 'li':
                    doc.add_paragraph(text, style='List Bullet')
                else:
                    doc.add_paragraph(text)
            return

    # Process children recursively for layout preservation
    if hasattr(elem, 'children'):
        for child in elem.children:
            process_element(child, doc, session, lesson_dir)

def process_block(block, course_dir: Path, lesson_title: str, block_idx: int, session):
    """Processes a single content block, extracting text, images and downloading videos."""
    lesson_dir = course_dir / clean_filename(lesson_title)
    lesson_dir.mkdir(parents=True, exist_ok=True)
    
    doc_path = lesson_dir / "content.docx"
    if doc_path.exists():
        try:
            doc = docx.Document(doc_path)
        except Exception:
            # Recreate if corrupted or not a valid docx
            doc = docx.Document()
            doc.add_heading(lesson_title, level=1)
    else:
        doc = docx.Document()
        doc.add_heading(lesson_title, level=1)
        
    title_elem = block.find(['h1', 'h2', 'h3', 'h4', 'strong', 'b'])
    if title_elem and title_elem.get_text(strip=True):
        block_title = title_elem.get_text(strip=True)
    else:
        block_title = f"Block_{block_idx}"
        
    doc.add_heading(block_title, level=2)
    
    # Process contents preserving order
    for child in block.children:
        process_element(child, doc, session, lesson_dir)
        
    doc.save(doc_path)
    logging.info(f"Saved text and images -> {doc_path.name}")

    # Extract videos
    videos = block.find_all(lambda tag: tag.name == 'iframe' or tag.has_attr('data-video-hash'))
    video_items = []
    
    for v in videos:
        if v.name == 'iframe' and v.get('src'):
            video_items.append(v['src'])
        elif v.has_attr('data-video-hash'):
            video_items.append(v['data-video-hash'])

    if video_items:
        # Download videos automatically using yt-dlp
        for idx, item in enumerate(video_items, start=1):
            target = item if "://" in item else f"https://player.getcourse.ru/video/hash/{item}"
            logging.info(f"Downloading video {idx}/{len(video_items)} in {lesson_title}...")
            
            out_tmpl = str(lesson_dir / f"%(title)s_%(id)s.%(ext)s")
            cmd = ["yt-dlp", "--cookies", "cookies.txt", "-o", out_tmpl, target]
            
            try:
                subprocess.run(cmd, cwd=Path(__file__).parent, check=True)
                logging.info(f"Successfully downloaded video: {target}")
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to download video {target}. yt-dlp returned error. Moving on...")
            except FileNotFoundError:
                logging.error("yt-dlp is not installed! Cannot download video. Please install it.")


def process_lesson(url: str, session, course_dir: Path, title: str):
    """Fetches a lesson page and extracts its content blocks."""
    logging.info(f"Fetching lesson: {title} ({url})")
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch lesson {url}: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    blocks = soup.select('.lite-block-live-wrapper, .v-spacing') 
    
    if not blocks:
        logging.warning(f"No content blocks found in lesson: {title}")
        return

    for idx, block in enumerate(blocks, start=1):
        process_block(block, course_dir, title, idx, session)
        
    time.sleep(1) # Polite delay between lessons

def crawl_course(url: str, session, data_dir: Path):
    """Crawls a stream/course page, finds lessons, and processes them."""
    logging.info(f"Crawling course stream: {url}")
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch course {url}: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    title_elem = soup.find('h1')
    course_title = title_elem.get_text(strip=True) if title_elem else "Course"
    course_dir = data_dir / clean_filename(course_title)
    course_dir.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"Course directory created: {course_dir.name}")
    
    lesson_links = soup.find_all('a', href=lambda href: href and '/lesson/view/id/' in href)
    
    if not lesson_links:
        logging.warning("No lesson links found. Is this a course stream page? Ensure cookies are valid.")
        process_lesson(url, session, data_dir, "Single_Lesson")
        return

    seen = set()
    lessons = []
    for a in lesson_links:
        href = a['href']
        if href not in seen:
            seen.add(href)
            title = a.get_text(strip=True)
            if not title:
                parent = a.find_parent('div', class_='stream-title') or a.find_parent('td')
                if parent:
                    title = parent.get_text(strip=True)
            if not title:
                title = f"Lesson_{len(lessons)+1}"
                
            full_url = urljoin(url, href)
            lessons.append((full_url, title))

    logging.info(f"Found {len(lessons)} lessons in this course.")
    
    for idx, (lesson_url, lesson_title) in enumerate(lessons, start=1):
        logging.info(f"[{idx}/{len(lessons)}] Processing {lesson_title}...")
        numbered_title = f"{idx:03d}_{lesson_title}"
        process_lesson(lesson_url, session, course_dir, numbered_title)

def scrape(url_or_path: str):
    """Main scraping orchestrator."""
    if requests is None or BeautifulSoup is None or docx is None:
        logging.error("Missing dependencies. Please run: pip install -r requirements.txt")
        sys.exit(1)

    base_dir = Path(__file__).parent.resolve()
    data_dir = base_dir / "data"
    data_dir.mkdir(exist_ok=True)
    
    session = get_session(base_dir)
    
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        crawl_course(url_or_path, session, data_dir)
    else:
        path = Path(url_or_path)
        if not path.exists():
            logging.error(f"File not found: {path}")
            return
        logging.info(f"Reading local file: {path}")
        html_content = path.read_text(encoding='utf-8')
        soup = BeautifulSoup(html_content, 'html.parser')
        blocks = soup.select('.lite-block-live-wrapper, .v-spacing')
        for idx, block in enumerate(blocks, start=1):
            process_block(block, data_dir, "Local_File", idx, session)

def main():
    parser = argparse.ArgumentParser(description="GetCourse clean and scalable scraper engine.")
    parser.add_argument('--url', type=str, help="URL of the stream/course to scrape")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.resolve()
    
    init_git_repo(base_dir)

    if args.url:
        scrape(args.url)
    else:
        logging.info("No URL provided. Git structure initialized. Use --url <course_stream_url> to start scraping.")

if __name__ == "__main__":
    main()
