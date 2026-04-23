import os
import sys
import time
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
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None

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
        requirements_path.write_text("requests\nbeautifulsoup4\n", encoding='utf-8')
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

def process_block(block, course_dir: Path, lesson_title: str, block_idx: int):
    """Processes a single content block, extracting text and video hashes."""
    
    # Attempt to find a sub-title inside the block, if any
    title_elem = block.find(['h1', 'h2', 'h3', 'h4', 'strong', 'b'])
    if title_elem and title_elem.get_text(strip=True):
        block_title = title_elem.get_text(strip=True)
    else:
        block_title = f"Block_{block_idx}"
        
    lesson_dir = course_dir / clean_filename(lesson_title)
    lesson_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract text content
    for tag in block(["script", "style"]):
        tag.decompose()
        
    text_content = block.get_text(separator='\n\n', strip=True)
    if text_content:
        md_file = lesson_dir / f"{clean_filename(block_title)}.md"
        # Append if exists (multiple blocks per lesson)
        mode = 'a' if md_file.exists() else 'w'
        with open(md_file, mode, encoding='utf-8') as f:
            if mode == 'w':
                f.write(f"# {lesson_title}\n\n")
            f.write(f"## {block_title}\n\n{text_content}\n\n")
        logging.info(f"Saved text content -> {md_file.name}")

    # Extract video iframes or hashes
    videos = block.find_all(lambda tag: tag.name == 'iframe' or tag.has_attr('data-video-hash'))
    video_items = []
    
    for v in videos:
        if v.name == 'iframe' and v.get('src'):
            video_items.append(v['src'])
        elif v.has_attr('data-video-hash'):
            video_items.append(v['data-video-hash'])

    if video_items:
        links_file = lesson_dir / "video_links.txt"
        mode = 'a' if links_file.exists() else 'w'
        with open(links_file, mode, encoding='utf-8') as f:
            f.write("\n".join(video_items) + "\n")
        logging.info(f"Saved {len(video_items)} video links -> {links_file.name}")
        
        # Generate yt-dlp commands
        ytdlp_log = lesson_dir / "yt-dlp_commands.log"
        cmds = []
        for item in video_items:
            target = item if "://" in item else f"https://player.getcourse.ru/video/hash/{item}"
            cmd = f"yt-dlp --cookies cookies.txt -o \"%(title)s.%(ext)s\" \"{target}\""
            cmds.append(cmd)
            
        mode = 'a' if ytdlp_log.exists() else 'w'
        with open(ytdlp_log, mode, encoding='utf-8') as f:
            f.write("\n".join(cmds) + "\n")

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
    blocks = soup.select('.lite-block-live-wrapper, .v-spacing') # Expanded selectors
    
    if not blocks:
        logging.warning(f"No content blocks found in lesson: {title}")
        return

    for idx, block in enumerate(blocks, start=1):
        process_block(block, course_dir, title, idx)
        
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
    
    # Try to find a course title
    title_elem = soup.find('h1')
    course_title = title_elem.get_text(strip=True) if title_elem else "Course"
    course_dir = data_dir / clean_filename(course_title)
    course_dir.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"Course directory created: {course_dir.name}")
    
    # Find all links to lessons. Usually contain /lesson/view/id/
    # Sometimes it's /teach/control/lesson/view/id/
    lesson_links = soup.find_all('a', href=lambda href: href and '/lesson/view/id/' in href)
    
    if not lesson_links:
        logging.warning("No lesson links found. Is this a course stream page? Ensure cookies are valid.")
        # If it's just a single lesson, fallback to parsing it directly
        process_lesson(url, session, data_dir, "Single_Lesson")
        return

    # Deduplicate lesson links while preserving order
    seen = set()
    lessons = []
    for a in lesson_links:
        href = a['href']
        if href not in seen:
            seen.add(href)
            # Find a title for the lesson
            title = a.get_text(strip=True)
            if not title:
                # sometimes title is in an adjacent element
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
    if requests is None or BeautifulSoup is None:
        logging.error("Missing dependencies. Please run: pip install -r requirements.txt")
        sys.exit(1)

    base_dir = Path(__file__).parent.resolve()
    data_dir = base_dir / "data"
    data_dir.mkdir(exist_ok=True)
    
    session = get_session(base_dir)
    
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        # We will assume it's a stream URL and try to crawl it
        crawl_course(url_or_path, session, data_dir)
    else:
        # Local file parsing fallback
        path = Path(url_or_path)
        if not path.exists():
            logging.error(f"File not found: {path}")
            return
        logging.info(f"Reading local file: {path}")
        html_content = path.read_text(encoding='utf-8')
        soup = BeautifulSoup(html_content, 'html.parser')
        blocks = soup.select('.lite-block-live-wrapper, .v-spacing')
        for idx, block in enumerate(blocks, start=1):
            process_block(block, data_dir, "Local_File", idx)

def main():
    parser = argparse.ArgumentParser(description="GetCourse clean and scalable scraper engine.")
    parser.add_argument('--url', type=str, help="URL of the stream/course to scrape")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.resolve()
    
    # 1. Initialize git repo if not exists
    init_git_repo(base_dir)

    # 2. Run scraper if URL is provided
    if args.url:
        scrape(args.url)
    else:
        logging.info("No URL provided. Git structure initialized. Use --url <course_stream_url> to start scraping.")

if __name__ == "__main__":
    main()
